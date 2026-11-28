from __future__ import annotations
from database.sqlite_db import broadcast_notification
import os
import re
import sys
import time
import threading
import queue
import subprocess
import json
from pathlib import Path
from datetime import datetime
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
from modules.parking.tracking_stabilizer import StationaryVehicleStabilizer, compute_box_iou
from modules.traffic.traffic_monitor import TrafficMonitor
from modules.ocr.ocr_manager import OCRManager
from modules.weapon.weapon_manager import WeaponManager
from modules.utils.alpr_logger import ALPRLogger
from modules.utils.traffic_alert_manager import TrafficAlertManager
from modules.utils.interactive_telegram_bot import start_bot_thread
from modules.utils.async_io_worker import AsyncIOWorker
from modules.utils.hw_jpeg_encoder import HardwareJPEGEncoder
from database.sqlite_db import (
    connect,
    log_passed_vehicle,
    log_congestion,
    update_congestion_end_time,
    log_parking_violation,
    update_violation_end_time,
    log_vehicle_count,
    log_detected_license_plate,
    update_congestion_heartbeat,
    update_violation_heartbeat
)
from collections import deque

# Nhãn nhận diện
TRAFFIC_LABELS = {"person", "bicycle", "car", "motorcycle", "bus", "truck"}
VEHICLE_LABELS = {"car", "motorcycle", "bus", "truck"}
PARKING_LABELS = {"car", "bus", "truck"}
WEAPON_LABELS = {"knife", "pistol", "sword"}
EXTRA_LABELS = {"helmet", "mask", "backpack"}
LICENSE_PLATE_LABELS = {"license_plate", "licenseplate", "number_plate", "licence_plate"}
DETECTABLE_LABELS = TRAFFIC_LABELS | LICENSE_PLATE_LABELS | WEAPON_LABELS | EXTRA_LABELS
SMALL_OBJECT_LABELS = {
    "person", "motorcycle", "bicycle", "car",
    "knife", "pistol", "sword", "helmet", "mask", "backpack",
}

# Ánh xạ tên mức độ ùn tắc cho tên thư mục ảnh
TRAFFIC_LEVEL_NAMES = {
    1: "DongDuc",
    2: "RatDong",
    3: "TacNghen"
}

# Kích thước pipe từ FFmpeg — 1080p (1920px) cho chất lượng cao
PIPE_WIDTH = 1920

# Kích thước preview để encode JPEG gửi lên web (MJPEG stream)
# 1280px (720p) là mức cân bằng tốt cho chất lượng và băng thông
PREVIEW_WIDTH = 1280

# Cache khả năng hỗ trợ phần cứng một lần để không probe lặp lại mỗi khi mở camera
_OPENCV_HAS_GSTREAMER = bool(re.search(r"GStreamer:\s+YES", cv2.getBuildInformation()))
_CACHED_FFMPEG_ENCODER: Optional[str] = None


def _get_preferred_ffmpeg_encoder() -> str:
    """Phát hiện bộ mã hóa H.264 phần cứng của FFmpeg một lần và lưu cache."""
    global _CACHED_FFMPEG_ENCODER
    if _CACHED_FFMPEG_ENCODER is not None:
        return _CACHED_FFMPEG_ENCODER
    encoder = "libx264"
    try:
        res = subprocess.run(["ffmpeg", "-encoders"], capture_output=True, text=True, timeout=2)
        if "h264_nvenc" in res.stdout:
            encoder = "h264_nvenc"
        elif "h264_v4l2m2m" in res.stdout:
            encoder = "h264_v4l2m2m"
    except Exception:
        pass
    _CACHED_FFMPEG_ENCODER = encoder
    return encoder


def _resolve_rtsp_push_enabled(settings: Optional[Dict[str, Any]] = None) -> bool:
    """Xác định có bật đẩy RTSP -> MediaMTX (phục vụ WebRTC) hay không."""
    if settings and "enable_rtsp_push" in settings and settings["enable_rtsp_push"] is not None:
        return bool(settings["enable_rtsp_push"])
    rtsp_env = os.environ.get("ENABLE_RTSP_PUSH", "").strip().lower()
    if rtsp_env in ("0", "false", "no", "off"):
        return False
    if rtsp_env in ("1", "true", "yes", "on"):
        return True
    try:
        from database.sqlite_db import get_system_setting
        wan_mode = str(get_system_setting("wan_transmission_mode", "webrtc")).strip().lower()
        if wan_mode == "mjpeg":
            return False
        db_val = str(get_system_setting("enable_rtsp_push", "true")).strip().lower()
        return db_val not in ("0", "false", "no", "off")
    except Exception:
        return True


def _read_exact_into_ndarray(pipe, shape: Tuple[int, int, int], frame_size: int) -> Optional[np.ndarray]:
    """Đọc thẳng frame raw từ stdout FFmpeg vào ndarray writable, tránh alloc bytes + copy() thừa."""
    buf = np.empty(frame_size, dtype=np.uint8)
    mv = memoryview(buf)
    offset = 0
    while offset < frame_size:
        n = pipe.readinto(mv[offset:])
        if not n:
            return None
        offset += n
    return buf.reshape(shape)

# Màu sắc cho các loại phương tiện và đối tượng nhận diện
BOX_COLORS = {
    "person": (0, 255, 0),
    "bicycle": (255, 127, 0),
    "car": (255, 255, 0),
    "motorcycle": (0, 255, 255),
    "bus": (0, 165, 255),
    "truck": (255, 0, 255),
    "license_plate": (255, 105, 180),
    "knife": (0, 0, 255),
    "pistol": (0, 0, 220),
    "sword": (0, 69, 255),
    "helmet": (255, 200, 0),
    "mask": (200, 180, 255),
    "backpack": (180, 130, 70),
}


class VideoStream:
    """Luồng đọc frame sử dụng FFmpeg CLI để giải mã thô (Raw Video).
    Giải pháp để tránh crash async_lock của OpenCV trên Windows với H.265.
    """
    def __init__(self, path, force_single_thread: bool = False, pre_probed: Optional[Tuple[int, int, float, int]] = None):
        self.path = str(path)
        self.stopped = False
        self.queue = queue.Queue(maxsize=5)
        self._is_opened = False
        # FIX #2: Chỉ dùng single-thread khi cần (H.265), H.264 dùng auto-thread
        self._force_single_thread = force_single_thread

        if pre_probed is not None:
            self.width, self.height, self.fps, self.total_frames = pre_probed
            self._is_opened = True
        else:
            # Lấy metadata bằng ffprobe 1 lần
            w, h, fps_val, tot, _ = _probe_stream_metadata(self.path)
            self.width, self.height, self.fps, self.total_frames = w, h, fps_val, tot
            self._is_opened = True

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
        self._cap = None
        self._using_gstreamer = False

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

    def _build_gstreamer_pipeline(self) -> str:
        """Xây dựng chuỗi GStreamer pipeline sử dụng phần cứng NVDEC trên Jetson."""
        width, height = self.draw_w, self.draw_h
        is_url = "://" in self.path or self.path.startswith("rtsp")
        is_hevc = self._force_single_thread # _force_single_thread được truyền vào bằng giá trị is_hevc
        
        if is_url:
            depay = "rtph265depay ! h265parse" if is_hevc else "rtph264depay ! h264parse"
            return (
                f"rtspsrc location={self.path} latency=200 ! "
                f"{depay} ! nvv4l2decoder ! "
                f"nvvidconv ! video/x-raw, width={width}, height={height}, format=BGRx ! "
                f"appsink drop=True sync=False"
            )
        else:
            path = self.path
            if not path.startswith("file://") and not is_url:
                import os
                path = os.path.abspath(path)
            elif path.startswith("file://"):
                path = path[7:] # Cắt bỏ file:// để lấy đường dẫn thật
            
            demux = "qtdemux ! h265parse" if is_hevc else "qtdemux ! h264parse"
            return (
                f"filesrc location=\"{path}\" ! {demux} ! "
                f"nvv4l2decoder ! nvvidconv ! video/x-raw, width={width}, height={height}, format=BGRx ! "
                f"appsink drop=False"
            )

    def _run_gstreamer_loop(self):
        pipeline = self._build_gstreamer_pipeline()
        print(f"[VideoStream] Đang khởi tạo GStreamer Pipeline: {pipeline}")
        cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        
        if not cap.isOpened():
            raise RuntimeError("GStreamer không thể mở nguồn video này.")
            
        self._cap = cap
        self._using_gstreamer = True
        is_url = "://" in self.path or self.path.startswith("rtsp")
        
        while not self.stopped:
            ret, frame = cap.read()
            if not ret:
                cap.release()
                if self.stopped:
                    break
                if is_url:
                    time.sleep(5)
                cap = cv2.VideoCapture(self._build_gstreamer_pipeline(), cv2.CAP_GSTREAMER)
                self._cap = cap
                continue
                
            if frame.shape[0] != self.draw_h or frame.shape[1] != self.draw_w:
                frame = cv2.resize(frame, (self.draw_w, self.draw_h))
                
            # Loại bỏ kênh Alpha (BGRx -> BGR) bằng CPU SIMD siêu nhanh
            if frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                
            if not self.queue.full():
                self.queue.put(frame)
            else:
                if is_url:
                    try:
                        self.queue.get_nowait()# Loại bỏ ngay frame cũ nhất đang nằm ở đầu hàng đợi
                        self.queue.put(frame)# Đẩy frame mới nhất vừa nhận vào cuối hàng đợi
                    except Exception: pass
                else:
                    self.queue.put(frame)# Đợi cho đến khi AI xử lý xong và giải phóng vị trí trong hàng đợi
                    
        if self._cap:
            self._cap.release()

    def _build_ffmpeg_cmd(self) -> list:
        """Xây dựng lệnh FFmpeg giải mã video/RTSP (giữ hành vi ổn định như trước commit f4bb2c5)."""
        cmd = [
            "ffmpeg", "-loglevel", "error",
            "-threads", "0"
        ]

        path_str = str(self.path)
        rtsp_transport = os.environ.get("RTSP_TRANSPORT", "").strip().lower()
        if path_str.lower().startswith("rtsp://") and rtsp_transport in ("tcp", "udp", "udp_multicast", "http"):
            cmd.extend(["-rtsp_transport", rtsp_transport])

        # Hỗ trợ giải mã phần cứng trên Jetson (NVDEC / V4L2)
        use_nvdec = os.environ.get("USE_JETSON_NVDEC", "0") == "1"
        if use_nvdec:
            # Tuỳ thuộc vào bản FFmpeg trên Jetpack, thường dùng h264_nvv4l2dec hoặc nvmpi
            cmd.extend(["-c:v", "h264_nvv4l2dec"])
            
        cmd.extend([
            "-i", path_str,
            "-vf", f"scale={self.draw_w}:{self.draw_h}",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-"
        ])
        return cmd

    def _run_ffmpeg_loop(self):
        self._using_gstreamer = False
        frame_size = self.draw_w * self.draw_h * 3
        shape = (self.draw_h, self.draw_w, 3)
        pipe_bufsize = max(frame_size * 2, 1024 * 1024)
        is_url = "://" in self.path or self.path.startswith("rtsp")

        self._proc = subprocess.Popen(
            self._build_ffmpeg_cmd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=pipe_bufsize,
        )

        while not self.stopped:
            try:
                frame = _read_exact_into_ndarray(self._proc.stdout, shape, frame_size)
                if frame is None:
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
                        stderr=subprocess.DEVNULL,
                        bufsize=pipe_bufsize,
                    )
                    continue

                if not self.queue.full():
                    self.queue.put(frame)
                else:
                    # Nếu là RTSP/Live stream thì mới drop frame cũ (tránh lag tích lũy)
                    if is_url:
                        try:
                            self.queue.get_nowait()
                            self.queue.put(frame)
                        except Exception:
                            pass
                    else:
                        # Nếu là Video File, chờ theo từng nhịp ngắn để vẫn kiểm tra được cờ self.stopped
                        while not self.stopped:
                            try:
                                self.queue.put(frame, timeout=0.2)
                                break
                            except queue.Full:
                                continue
            except Exception as e:
                print(f"[VideoStream FFmpeg] Lỗi luồng đọc: {e}")
                if not self.stopped:
                    time.sleep(1)

    def update(self):
        use_gstreamer = (os.environ.get("USE_GSTREAMER", "1") == "1") and (
            _OPENCV_HAS_GSTREAMER or os.environ.get("FORCE_GSTREAMER", "0") == "1"
        )
        if use_gstreamer:
            try:
                self._run_gstreamer_loop()
                return
            except Exception as e:
                print(f"[VideoStream] GStreamer không khả dụng ({e}). Đang chuyển qua FFmpeg dự phòng...")
        
        self._run_ffmpeg_loop()

    def read(self):
        if self.queue.empty() and self.stopped:
            return False, None
        try:
            return True, self.queue.get(timeout=2.0)
        except queue.Empty:
            return False, None

    def release(self):
        self.stopped = True
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except Exception:
                break
        if self._using_gstreamer and self._cap:
            self._cap.release()
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1)
            except Exception:
                if self._proc:
                    self._proc.kill()
            finally:
                try:
                    if self._proc.stdout:
                        self._proc.stdout.close()
                except Exception:
                    pass

    def set_pos(self, frame_idx):
        # Reset bằng cách restart process
        if self._using_gstreamer and self._cap:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        elif self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass


def _canonical_label(label: Any) -> str:
    """Chuẩn hóa label về dạng snake_case."""
    if not isinstance(label, str):
        return str(label)
    return label.strip().lower().replace(" ", "_")

def create_paddle_ocr() -> "PaddleOCR":
    """Tạo instance PaddleOCR, tự động nhận diện biến môi trường cho ONNX và cấu hình TensorRT Caching."""
    import os
    if os.name == 'nt':
        try:
            import site
            for s_dir in (site.getsitepackages() if hasattr(site, "getsitepackages") else []):
                trt_dir = os.path.join(s_dir, "tensorrt_libs")
                if os.path.isdir(trt_dir):
                    if hasattr(os, "add_dll_directory"):
                        os.add_dll_directory(trt_dir)
                    os.environ["PATH"] = trt_dir + os.pathsep + os.environ["PATH"]
                    break
        except Exception:
            pass
            
    use_onnx_mode = os.environ.get("USE_PADDLE_ONNX", "0")
    
    if use_onnx_mode in ("1", "2"):
        if use_onnx_mode == "1":
            os.makedirs("./trt_cache", exist_ok=True)
            
            try:
                import onnxruntime as ort
                if not hasattr(ort, "_patched_by_cityvision"):
                    _original_InferenceSession = ort.InferenceSession
                    
                    def _patched_InferenceSession(path_or_bytes, sess_options=None, providers=None, provider_options=None, **kwargs):
                        if isinstance(path_or_bytes, (str, Path)) and os.path.isdir(path_or_bytes):
                            path_or_bytes = os.path.join(str(path_or_bytes), "model.onnx")
                            
                        model_path = str(path_or_bytes).lower()
                        
                        # Cấu hình lõi chung cho mọi model
                        trt_options = {
                            'device_id': 0,
                            'trt_fp16_enable': True,
                            'trt_engine_cache_enable': True,
                            'trt_engine_cache_path': './trt_cache',
                        }

                        # Bơm Dynamic Shapes tùy thuộc vào mô hình đang được load
                        if "rec" in model_path:
                            # Profile cho mô hình Nhận diện chữ (Batch 1-10, Width 10-1000)
                            trt_options['trt_profile_min_shapes'] = 'x:1x3x48x10'
                            trt_options['trt_profile_opt_shapes'] = 'x:6x3x48x320'
                            trt_options['trt_profile_max_shapes'] = 'x:10x3x48x1000'
                        elif "det" in model_path:
                            # Profile cho mô hình Phát hiện vùng chữ (Batch 1-4)
                            trt_options['trt_profile_min_shapes'] = 'x:1x3x32x32'
                            trt_options['trt_profile_opt_shapes'] = 'x:1x3x384x640'
                            trt_options['trt_profile_max_shapes'] = 'x:4x3x768x960'

                        trt_providers = [
                            ('TensorrtExecutionProvider', trt_options),
                            'CUDAExecutionProvider',
                            'CPUExecutionProvider'
                        ]
                        
                        session = _original_InferenceSession(path_or_bytes, sess_options, trt_providers, None, **kwargs)
                        print(f"[ONNX GPU] Đã kích hoạt lõi TensorRT cho model {os.path.basename(model_path)}.")
                        print(f"[ONNX GPU] Các bộ xử lý đang chạy: {session.get_providers()}")
                        
                        return session
                    
                    ort.InferenceSession = _patched_InferenceSession
                    ort._patched_by_cityvision = True
            except ImportError:
                print("Cảnh báo: onnxruntime không khả dụng!")
        else:
            # use_onnx_mode == "2": Chỉ dùng CUDA và CPU, không patch TensorRT để tránh sinh cache .engine
            try:
                import onnxruntime as ort
                if not hasattr(ort, "_patched_by_cityvision"):
                    _original_InferenceSession = ort.InferenceSession
                    
                    def _patched_InferenceSession(path_or_bytes, sess_options=None, providers=None, provider_options=None, **kwargs):
                        if isinstance(path_or_bytes, (str, Path)) and os.path.isdir(path_or_bytes):
                            path_or_bytes = os.path.join(str(path_or_bytes), "model.onnx")
                            
                        model_path = str(path_or_bytes).lower()
                        cuda_providers = [
                            'CUDAExecutionProvider',
                            'CPUExecutionProvider'
                        ]
                        session = _original_InferenceSession(path_or_bytes, sess_options, cuda_providers, None, **kwargs)
                        print(f"[ONNX CUDA] Khởi tạo model {os.path.basename(model_path)} qua CUDA.")
                        print(f"[ONNX CUDA] Các bộ xử lý đang chạy: {session.get_providers()}")
                        return session
                        
                    ort.InferenceSession = _patched_InferenceSession
                    ort._patched_by_cityvision = True
            except ImportError:
                print("Cảnh báo: onnxruntime không khả dụng!")

        # Import PaddleOCR tại đây để đảm bảo onnxruntime đã được patch TRƯỚC khi PaddleOCR gọi tới
        from paddleocr import PaddleOCR 

        det_dir = os.environ.get("PADDLE_ONNX_DET_DIR", None)
        rec_dir = os.environ.get("PADDLE_ONNX_REC_DIR", None)
        cls_dir = os.environ.get("PADDLE_ONNX_CLS_DIR", None)
        
        ocr_args = {"use_angle_cls": True, "lang": "en", "use_onnx": True, "show_log": True}
        # Bắt buộc phải có đủ mô hình DET và REC.
        # Dù YOLO đã cắt biển số, nhưng biển số xe VN có 2 dòng chữ, 
        # model DET của OCR vẫn cần thiết để tách dòng trước khi nạp vào REC.
        if det_dir: ocr_args["det_model_dir"] = det_dir
        if rec_dir: ocr_args["rec_model_dir"] = rec_dir
        if cls_dir: ocr_args["cls_model_dir"] = cls_dir
        
        # Bật log tạm thời để bạn thấy được TensorRT khởi động
        import logging
        logging.getLogger("ppocr").setLevel(logging.INFO)
        return PaddleOCR(**ocr_args)
    else:
        from paddleocr import PaddleOCR
        import logging
        logging.getLogger("ppocr").setLevel(logging.ERROR)
        return PaddleOCR(lang="en", use_gpu=False, show_log=False)


def _display_label(label_key: str) -> str:
    """Chuyển key label sang dạng hiển thị."""
    return label_key.replace("_", " ")


def _probe_stream_metadata(path: Any) -> Tuple[int, int, float, int, bool]:
    """Đọc toàn bộ metadata video/RTSP (width, height, fps, total_frames, is_hevc) bằng một lần ffprobe duy nhất."""
    path_str = str(path)
    if not isinstance(path, int):
        try:
            cmd = ["ffprobe", "-v", "error"]
            if path_str.lower().startswith("rtsp://"):
                cmd.extend(["-rtsp_transport", "tcp"])
            cmd.extend([
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,width,height,r_frame_rate,nb_frames",
                "-of", "json",
                path_str,
            ])
            probe = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            info = json.loads(probe.stdout)
            streams = info.get("streams") or [{}]
            stream_info = streams[0] if isinstance(streams, list) and streams else {}
            width = int(stream_info.get("width") or 1280)
            height = int(stream_info.get("height") or 720)

            fps_str = str(stream_info.get("r_frame_rate") or "25/1").strip()
            try:
                if "/" in fps_str:
                    num, den = fps_str.split("/", 1)
                    raw_fps = float(num) / float(den) if float(den) > 0 else 25.0
                else:
                    raw_fps = float(fps_str)
            except (ValueError, ZeroDivisionError):
                raw_fps = 25.0
            fps = raw_fps if raw_fps > 0 else 25.0

            raw_nb_frames = stream_info.get("nb_frames")
            try:
                total_frames = int(raw_nb_frames) if raw_nb_frames not in (None, "", "N/A") else 0
            except (ValueError, TypeError):
                total_frames = 0
            codec_name = str(stream_info.get("codec_name", "")).strip().lower()
            is_hevc = codec_name in ("hevc", "h265")
            return width, height, fps, total_frames, is_hevc
        except Exception as e:
            print(f"[VideoStream] Lỗi lấy metadata bằng ffprobe ({e}), fallback OpenCV...")

    width, height, fps, total_frames = 1280, 720, 25.0, 0
    cap_arg = path if isinstance(path, int) else path_str
    cap = cv2.VideoCapture(cap_arg)
    if cap.isOpened():
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
    return width, height, fps, total_frames, False


def _is_hevc(path: Any) -> bool:
    """Kiểm tra video có dùng codec H.265/HEVC không."""
    _, _, _, _, is_hevc = _probe_stream_metadata(path)
    return is_hevc


_DEFAULT_ENGINE_CLASS_NAMES: Dict[int, str] = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    4: "license_plate",
    5: "bus",
    6: "truck",
    7: "knife",
    8: "pistol",
    9: "sword",
    10: "helmet",
    11: "mask",
    12: "backpack",
}


def _normalize_names_dict(raw_names: Any) -> Optional[Dict[int, str]]:
    """Chuẩn hóa bảng tên class (dict hoặc list/tuple) sang Dict[int, str]."""
    if isinstance(raw_names, dict):
        parsed: Dict[int, str] = {}
        for k, v in raw_names.items():
            try:
                parsed[int(k)] = str(v)
            except (ValueError, TypeError):
                continue
        return parsed or None
    if isinstance(raw_names, (list, tuple)) and raw_names:
        return {idx: str(v) for idx, v in enumerate(raw_names)}
    return None


def _is_valid_detectable_names(names_dict: Optional[Dict[int, str]]) -> bool:
    """Kiểm tra bảng tên class có phải nhãn thật (không phải placeholder class0/class1...)."""
    if not names_dict:
        return False
    return any(_canonical_label(v) in DETECTABLE_LABELS for v in names_dict.values())


def _read_engine_names_metadata(model_path: str) -> Optional[Dict[int, str]]:
    """Đọc metadata names nhúng ở đầu file TensorRT .engine (nếu export từ Ultralytics)."""
    try:
        with open(model_path, "rb") as f:
            header = f.read(4)
            if len(header) < 4:
                return None
            meta_len = int.from_bytes(header, byteorder="little", signed=False)
            if meta_len <= 2 or meta_len > 1_000_000:
                return None
            meta_bytes = f.read(meta_len)
            if len(meta_bytes) != meta_len:
                return None
        metadata = json.loads(meta_bytes.decode("utf-8"))
        if not isinstance(metadata, dict):
            return None
        parsed = _normalize_names_dict(metadata.get("names"))
        return parsed if _is_valid_detectable_names(parsed) else None
    except Exception:
        return None


def _build_label_lookup(model: Optional[YOLO]) -> Tuple[Dict[int, str], Dict[int, str]]:
    """Tạo bảng ánh xạ cls_id -> (canonical_label, display_label) một lần sau khi tải model."""
    if model is None:
        return {}, {}
    raw_names = getattr(model, "custom_names", None)
    if not raw_names:
        raw_names = getattr(model, "names", None)
    normalized = _normalize_names_dict(raw_names) or {}
    canon_map: Dict[int, str] = {}
    disp_map: Dict[int, str] = {}
    for k, v in normalized.items():
        c_lbl = _canonical_label(v)
        canon_map[k] = c_lbl
        disp_map[k] = _display_label(c_lbl)
    return canon_map, disp_map


def _draw_detection_box(
    frame: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    box_color: Tuple[int, int, int],
    display_label: str,
    f_scale: float, f_thick: int, show_label: bool
) -> None:
    """Vẽ bounding box và nhãn đối tượng lên frame."""
    cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, f_thick)
    if display_label and show_label:
        (tw, th), baseline = cv2.getTextSize(display_label, cv2.FONT_HERSHEY_SIMPLEX, f_scale, f_thick)
        text_y = max(th + 10, y1 - 5)
        cv2.rectangle(frame, (x1, text_y - th - 10), (x1 + tw + 10, text_y + baseline + 5), box_color, -1)
        brightness = 0.114 * box_color[0] + 0.587 * box_color[1] + 0.299 * box_color[2]
        text_color = (0, 0, 0) if brightness > 165 else (255, 255, 255)
        cv2.putText(frame, display_label, (x1 + 5, text_y), cv2.FONT_HERSHEY_SIMPLEX, f_scale, text_color, f_thick)


def _draw_plate_overlay(
    frame: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    color: Tuple[int, int, int],
    display_text: str,
    f_scale: float, f_thick: int, f_offset: int
) -> None:
    """Vẽ lại khung biển số đã cached ở những frame skip."""
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), f_thick)
    (tw, th), baseline = cv2.getTextSize(display_text, cv2.FONT_HERSHEY_SIMPLEX, f_scale, f_thick)
    text_y = max(th + 5, y1 - f_offset)
    cv2.rectangle(frame, (x1, text_y - th - 5), (x1 + tw + 5, text_y + baseline + 2), color, -1)
    cv2.putText(frame, display_text, (x1 + 2, text_y), cv2.FONT_HERSHEY_SIMPLEX, f_scale, (255, 255, 255), f_thick)


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


def _normalize_parking_zones(raw_points: Any, width: int, height: int, metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Chuẩn hóa dữ liệu vùng cấm đỗ. Hỗ trợ cả định dạng polygon đơn (legacy) và đa vùng (multi-zone ROI).
    Trả về danh sách các vùng: [{'id': int, 'name': str, 'polygon': np.ndarray, 'timeout': Optional[float]}]
    """
    metadata = metadata or {}
    zones = metadata.get("zones")
    
    # 1. Trường hợp có cấu trúc đa vùng (multi-zone)
    if zones and isinstance(zones, list) and len(zones) > 0:
        normalized_zones = []
        for idx, z in enumerate(zones):
            if not isinstance(z, dict):
                continue
            z_pts = z.get("points")
            if not z_pts:
                continue
            norm_pts = _normalize_points(z_pts, width, height, metadata)
            poly = _to_polygon(norm_pts)
            if poly is not None and len(poly) >= 3:
                z_timeout = z.get("timeout")
                try:
                    z_timeout = float(z_timeout) if z_timeout is not None and str(z_timeout).strip() != "" else None
                except (ValueError, TypeError):
                    z_timeout = None
                normalized_zones.append({
                    "id": z.get("id", idx + 1),
                    "name": str(z.get("name") or f"Vùng {idx + 1}"),
                    "polygon": poly,
                    "timeout": z_timeout
                })
        if normalized_zones:
            return normalized_zones

    # 2. Fallback: Trường hợp polygon đơn lẻ truyền thống
    single_pts = _normalize_points(raw_points, width, height, metadata)
    single_poly = _to_polygon(single_pts)
    if single_poly is not None and len(single_poly) >= 3:
        return [{
            "id": 1,
            "name": "Vùng cấm",
            "polygon": single_poly,
            "timeout": None
        }]
    return []


def _full_frame_polygon(width: int, height: int) -> List[List[int]]:
    """Tạo polygon phủ toàn bộ frame."""
    return [
        [0, 0],
        [max(0, width - 1), 0],
        [max(0, width - 1), max(0, height - 1)],
        [0, max(0, height - 1)],
    ]


def _get_drawing_params(width: int) -> Tuple[float, int, int]:
    """
    Tính toán các tham số vẽ (fontScale, thickness, offset) dựa trên chiều rộng frame.
    Lấy chuẩn là 1280px (HD). Đã tinh chỉnh nhỏ hơn theo yêu cầu.
    """
    base_width = 1280
    ratio = width / base_width

    font_scale = max(0.38, 0.45 * ratio)
    thickness = max(1, int(round(1.2 * ratio)))
    offset = max(8, int(round(12 * ratio)))

    return font_scale, thickness, offset


def _is_frame_valid(frame) -> bool:
    """
    Kiểm tra nhanh xem frame RTSP có bị lỗi do mất gói tin (packet loss) không.
    Sử dụng subsampling (bước nhảy 32px) và cv2.meanStdDev chạy bằng C++ SIMD,
    chỉ mất ~0.03ms (nhanh gấp >1500 lần so với tính toàn bộ frame 1080p bằng numpy),
    hoàn toàn không tốn CPU và không gây tụt FPS trên Jetson.
    """
    if frame is None:
        return False
    if not isinstance(frame, np.ndarray) or frame.ndim < 3 or frame.shape[2] < 3:
        return False
    h, w = frame.shape[:2]
    if h < 32 or w < 32:
        return False

    # Lấy mẫu thưa bước nhảy 32: chỉ ~2,000 pixels thay vì 2,073,600 pixels (giảm 99.9% tải CPU)
    sample = frame[::32, ::32]

    # 1. Kiểm tra tỷ lệ trắng / đen trên ảnh mẫu
    gray_sample = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    white_ratio = float((gray_sample >= 250).mean())
    black_ratio = float((gray_sample <= 5).mean())
    if white_ratio > 0.40 or black_ratio > 0.70:
        return False

    # 2. Tính trung bình và độ lệch chuẩn bằng C++ SIMD (cv2.meanStdDev)
    mean, std = cv2.meanStdDev(sample)
    b_mean, g_mean, r_mean = mean[0][0], mean[1][0], mean[2][0]
    total_std = (std[0][0] + std[1][0] + std[2][0]) / 3.0

    # 3. Khung hình rỗng / đơn sắc (std quá thấp)
    if total_std < 3.0:
        return False

    # 4. Nhiễu giải mã H.264 (vệt xanh lá hoặc hồng lệch màu nghiêm trọng)
    if g_mean > 140 and (r_mean + b_mean) / 2.0 < 90 and g_mean > (r_mean + b_mean):
        return False
    if r_mean > 150 and b_mean > 150 and g_mean < 60:
        return False

    return True


def _load_model(model_path: Path) -> YOLO:
    """Tải model YOLO, hỗ trợ TensorRT .engine."""
    model_str = str(model_path)
    if model_str.lower().endswith(".engine"):
        model = YOLO(model_str, task="detect")
        # Đọc metadata names trực tiếp từ header .engine (nếu export từ Ultralytics) để tránh
        # kích hoạt lazy setup_model sớm và tránh nhận nhầm placeholder class0..class998 của trtexec.
        meta_names = _read_engine_names_metadata(model_str)
        model.custom_names = meta_names if meta_names else dict(_DEFAULT_ENGINE_CLASS_NAMES)
        return model

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
        input_str = str(input_path).strip().strip('"').strip("'")
        if input_str.startswith(("rtsp://", "http://", "https://", "rtmp://")) or input_str.isdigit():
            input_video_path = input_str if not input_str.isdigit() else int(input_str)
            should_cleanup_temp = False
        else:
            input_video_path = Path(input_path)
            should_cleanup_temp = False
    else:
        raise ValueError("Yêu cầu cung cấp input_stream hoặc input_path")

    if isinstance(input_video_path, Path) and not input_video_path.exists():
        raise FileNotFoundError(f"Không tìm thấy video đầu vào: {input_video_path}")

    # Gọi ffprobe 1 lần duy nhất lấy toàn bộ metadata cho cả remux và VideoStream
    probed_w, probed_h, probed_fps, probed_total, is_hevc = _probe_stream_metadata(input_video_path)

    # Chỉ remux nếu là file cục bộ (Path), không remux RTSP/URL
    if is_hevc and isinstance(input_video_path, Path):
        remuxed_path = _remux_to_faststart(input_video_path)
        if remuxed_path != input_video_path:
            if should_cleanup_temp:
                try:
                    os.unlink(input_video_path)
                except Exception:
                    pass
            input_video_path = remuxed_path
            should_cleanup_temp = True

    enable_ai = bool(settings.get("enable_ai", True))

    # Các cài đặt từ settings
    model_path = Path(str(settings["model_path"]))
    if enable_ai and not model_path.exists():
        raise FileNotFoundError(f"Không tìm thấy mô hình YOLO tại: {model_path}")

    # Các ngưỡng cấu hình
    confidence_threshold = float(settings.get("confidence_threshold", 0.25))
    enable_congestion = bool(settings.get("enable_congestion", True)) if enable_ai else False
    enable_illegal_parking = bool(settings.get("enable_illegal_parking", True)) if enable_ai else False
    enable_license_plate = bool(settings.get("enable_license_plate", True)) if enable_ai else False
    enable_weapon_detection = bool(settings.get("enable_weapon_detection", settings.get("bat_phat_hien_vu_khi", True))) if enable_ai else False
    enabled_detect_labels = set(settings["enabled_detect_labels"]) if settings.get("enabled_detect_labels") else None
    enabled_draw_labels = set(settings["enabled_draw_labels"]) if settings.get("enabled_draw_labels") else None
    stop_seconds = float(settings.get("stop_seconds", 30.0))
    move_threshold_px = float(settings.get("parking_move_threshold_px", 10.0))
    process_stride = max(1, int(settings.get("process_every_n_frames", 2)))
    save_to_db = bool(settings.get("save_to_db", True))

    # Cấu hình hiển thị (Overlay settings)
    show_roi_surveillance = bool(settings.get("show_roi_surveillance", False))
    show_roi_parking = bool(settings.get("show_roi_parking", False))
    show_fps = bool(settings.get("show_fps", True))
    show_box_map = {
        "person": bool(settings.get("show_box_person", True)),
        "bicycle": bool(settings.get("show_box_bicycle", True)),
        "car": bool(settings.get("show_box_car", True)),
        "motorcycle": bool(settings.get("show_box_motorcycle", True)),
        "license_plate": bool(settings.get("show_box_plate", True)),
        "bus": bool(settings.get("show_box_bus", True)),
        "truck": bool(settings.get("show_box_truck", True)),
        "knife": bool(settings.get("show_box_knife", True)),
        "pistol": bool(settings.get("show_box_pistol", True)),
        "sword": bool(settings.get("show_box_sword", True)),
        "helmet": bool(settings.get("show_box_helmet", True)),
        "mask": bool(settings.get("show_box_mask", True)),
        "backpack": bool(settings.get("show_box_backpack", True)),
    }
    show_label = bool(settings.get("show_label", True))

    # Truyền metadata đã probe sẵn để tránh chạy ffprobe lần 2
    capture = VideoStream(
        input_video_path,
        force_single_thread=is_hevc,
        pre_probed=(probed_w, probed_h, probed_fps, probed_total)
    ).start()
    is_live = not isinstance(input_video_path, Path)
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

    parking_zones = _normalize_parking_zones(
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
                "latest_status": "Đang tải model...",
            }
        )

    model = _load_model(model_path) if enable_ai else None
    canon_names_map, disp_names_map = _build_label_lookup(model)
    congestion_threshold = float(settings.get("congestion_threshold", 35.0))
    traffic_monitor = TrafficMonitor(roi_polygon=roi_polygon, congestion_threshold=congestion_threshold) if enable_congestion else None

    # AsyncIOWorker: Xử lý I/O nền (Telegram, ghi file, ghi DB)
    io_worker = AsyncIOWorker(num_threads=2, max_queue_size=200)
    io_worker.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    io_worker.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    io_worker.start()

    # Khởi tạo các Manager (inject io_worker)
    camera_id = int(settings.get("camera_id", 0))
    weapon_manager = WeaponManager(camera_id=camera_id, camera_name=settings.get("name", "Camera")) if enable_weapon_detection else None
    alpr_logger = ALPRLogger(db_callback=log_detected_license_plate, id_camera=camera_id)
    alpr_logger.io_worker = io_worker
    alpr_logger.save_to_db = save_to_db

    traffic_alert_manager = TrafficAlertManager()
    traffic_alert_manager.io_worker = io_worker
    traffic_alert_manager.save_to_db = False # Tắt tự ghi DB để tránh ghi đúp (tiến trình chính sẽ quản lý và lưu)
    traffic_alert_manager.camera_id = camera_id
    traffic_alert_manager.telegram_enabled = bool(settings.get("telegram_enabled", save_to_db))

    parking_manager = ParkingManager(None, None)
    parking_manager.stop_seconds = stop_seconds
    parking_manager.set_parking_zones(parking_zones)
    parking_manager.move_thr_px = move_threshold_px
    parking_manager.camera_id = camera_id
    parking_manager.violation_callback = log_parking_violation if save_to_db else None
    parking_manager.violation_end_callback = update_violation_end_time if save_to_db else None
    parking_manager.save_to_db = save_to_db
    parking_manager.io_worker = io_worker
    parking_manager.setup_detection(fps)

    parking_manager.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    parking_manager.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    # Cấu hình tracker ByteTrack: ưu tiên file tùy chỉnh cho xe tĩnh (bytetrack_parking.yaml)
    bytetrack_parking_cfg = PROJECT_ROOT / "core" / "bytetrack_parking.yaml"
    tracker_config_path = str(bytetrack_parking_cfg) if bytetrack_parking_cfg.exists() else "bytetrack.yaml"

    # Khởi tạo Bộ ổn định ID xe tĩnh (chống nhảy ID, trùng lặp ID, gộp cùng biển số/cùng vị trí)
    stationary_stabilizer = StationaryVehicleStabilizer(iou_thresh=0.35, iom_thresh=0.50, max_ghost_frames=75)

    # Không gọi start_bot_thread ở đây để tránh lỗi 409 Conflict từ Telegram
    # (Chỉ duy nhất tiến trình chính mới được phép gọi bot.polling())

    # Flags quản lý job
    logged_vehicle_ids: set = set()
    logged_violation_track_ids: set = set()
    last_db_traffic_level = 0
    last_congestion_record_id = None
    unique_passed_count = 0
    clear_start_time = 0
    true_clear_seconds = 5.0
    
    # Logic quản lý "Không phát hiện biển số"
    VEHICLE_LOG_LABELS = {"car", "truck", "bus", "motorcycle", "bicycle"}
    VEHICLE_NO_IMAGE_LABELS = {"motorcycle", "bicycle"}  # Xe máy/xe đạp: chỉ thống kê, không lưu ảnh
    pending_alpr_tracks = {} # track_id -> { "last_seen": frame_idx, "best_image": (frame, bbox) | None, "seen_count": int, "label": str }

    frame_index = 0
    consecutive_invalid_frames = 0  # Count consecutive corrupt frames
    MAX_INVALID_FRAMES = 30  # If >30 corrupt frames in a row -> treat as dead stream
    last_results = None
    started_at = time.time()
    latest_status = ""
    fps_prev_time = started_at
    fps_frame_count = 0
    current_fps = 0.0
    accumulated_process_time = 0.0
    last_heartbeat_time = started_at
    diag_prev_time = started_at

    # Luồng nền nén ảnh JPEG & đẩy trực tiếp qua progress_callback ngay khi nén xong
    _stream_quality_profiles = {
        "low": (854, 480, 40, 854, 480, "800K", "28"),
        "medium": (1280, 720, 70, 1280, 720, "1.8M", "23"),
        # Giữ nguyên độ phân giải 1080p cho high, q=75 giảm ~25% byte MJPEG và ~32% CPU nén
        "high": (1920, 1080, 75, 1920, 1080, "3.5M", "20"),
        "ultra": (1920, 1080, 88, 1920, 1080, "6M", "16"),
    }
    _init_profile = _stream_quality_profiles.get(settings.get("requested_quality"))
    _init_pw, _init_ph, _init_q = (
        (_init_profile[0], _init_profile[1], _init_profile[2])
        if _init_profile
        else (preview_w, preview_h, 75)
    )

    preview_lock = threading.Lock()
    preview_queue = queue.Queue(maxsize=1)
    preview_state = {
        "last_jpeg": None,
        "jpeg_seq": 0,
        "enc_count": 0,
        "stop": False,
        "pw": _init_pw,
        "ph": _init_ph,
        "q": _init_q,
        "viewer_count": 0,
        "force_preview": False,
        "last_emit_time": 0.0,
        "mp4_next_deadline": None,
    }
    progress_cb_lock = threading.Lock()
    pending_actions_lock = threading.Lock()
    pending_actions: Dict[str, Any] = {}

    def _emit_progress(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if progress_callback is None:
            return None
        with progress_cb_lock:
            resp = progress_callback(payload)
        if resp:
            if "viewer_count" in resp:
                preview_state["viewer_count"] = resp["viewer_count"]
            if resp.get("force_preview"):
                preview_state["force_preview"] = True
            with pending_actions_lock:
                if resp.get("abort"):
                    pending_actions["abort"] = True
                if "new_quality" in resp:
                    pending_actions["new_quality"] = resp["new_quality"]
                if "new_settings" in resp:
                    merged = dict(pending_actions.get("new_settings") or {})
                    merged.update(resp["new_settings"])
                    pending_actions["new_settings"] = merged
        return resp

    # Khởi tạo Hardware JPEG Encoder
    hw_encoder = HardwareJPEGEncoder()

    def _encode_preview_frame(f, w, h):
        return hw_encoder.encode(f, w, h, 75)

    def preview_encoder_worker():
        while not preview_state["stop"]:
            try:
                item = preview_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None or preview_state["stop"]:
                break

            frame_to_encode, enc_frame_idx, enc_status = item
            is_forced = preview_state["force_preview"]
            # Chỉ nén ảnh nếu có người xem hoặc có cờ yêu cầu bắt buộc nén
            if preview_state["viewer_count"] > 0 or is_forced:
                encoded_jpg = hw_encoder.encode(
                    frame_to_encode,
                    preview_state["pw"],
                    preview_state["ph"],
                    preview_state["q"]
                )
                if encoded_jpg and not preview_state["stop"]:
                    now_enc = time.time()
                    with preview_lock:
                        preview_state["last_jpeg"] = encoded_jpg
                        preview_state["jpeg_seq"] += 1
                        preview_state["enc_count"] += 1
                    if progress_callback is not None:
                        preview_state["last_emit_time"] = now_enc
                        _emit_progress({
                            "phase": "running_detection",
                            "latest_status": enc_status,
                            "preview_jpeg": encoded_jpg,
                            "timestamp": now_enc,
                            "processed_frames": enc_frame_idx,
                        })
                # Nếu là force_preview thì tắt đi sau khi nén xong để không nén thừa mứa
                if is_forced:
                    preview_state["force_preview"] = False

    preview_thread = threading.Thread(target=preview_encoder_worker, args=(), daemon=True)
    preview_thread.start()

    # OCR Manager setup
    import logging
    logging.getLogger("ppocr").setLevel(logging.ERROR)
    ocr_reader = create_paddle_ocr() if enable_license_plate else None
    ocr_manager = OCRManager(ocr_reader, alpr_logger=alpr_logger) if enable_license_plate else None
    if ocr_manager is not None:
        ocr_manager.save_to_db = save_to_db
        ocr_manager.start_worker()

    # Khởi chạy FFmpeg RTSP push process + Worker ghi bất đồng bộ (không block luồng AI)
    enable_rtsp_push = _resolve_rtsp_push_enabled(settings)
    rtsp_proc = None
    rtsp_lock = threading.RLock()
    rtsp_target = f"rtsp://127.0.0.1:8554/live_camera_{camera_id}"

    encoder_to_use = _get_preferred_ffmpeg_encoder() if enable_rtsp_push else "disabled"
    if enable_rtsp_push:
        if encoder_to_use == "h264_nvenc":
            print(f"[RTSP Push - Camera {camera_id}] Sử dụng GPU NVENC (cache) để nén video!")
        elif encoder_to_use == "h264_v4l2m2m":
            print(f"[RTSP Push - Camera {camera_id}] Sử dụng Jetson V4L2 Hardware Encoder (cache)!")
        else:
            print(f"[RTSP Push - Camera {camera_id}] Sử dụng CPU encoder (libx264)...")
    else:
        print(f"[RTSP Push - Camera {camera_id}] Đã tắt đẩy RTSP nền (dùng trực tiếp MJPEG).")
    rtsp_start_time = 0.0

    # Kích thước push mặc định giới hạn ở 720p (PREVIEW_WIDTH) để nhẹ CPU/encoder trên Jetson;
    # có thể tăng lên 1080p khi chọn high/ultra trong menu chất lượng.
    if draw_w > PREVIEW_WIDTH:
        push_w = PREVIEW_WIDTH
        push_h = int(draw_h * (PREVIEW_WIDTH / draw_w))
    else:
        push_w, push_h = draw_w, draw_h
    push_bitrate = "1.8M"
    push_crf = "23"
    
    # Lấy chất lượng ban đầu nếu có yêu cầu từ settings
    initial_q = settings.get("requested_quality")
    if initial_q:
        if initial_q == "low":
            push_w, push_h, push_bitrate, push_crf = 854, 480, "800K", "28"
        elif initial_q == "medium":
            push_w, push_h, push_bitrate, push_crf = 1280, 720, "1.8M", "23"
        elif initial_q == "high":
            push_w, push_h, push_bitrate, push_crf = 1920, 1080, "3.5M", "20"
        elif initial_q == "ultra":
            push_w, push_h, push_bitrate, push_crf = 1920, 1080, "6M", "16"

    # Đảm bảo chia hết cho 2
    push_w = push_w + (push_w % 2)
    push_h = push_h + (push_h % 2)

    rtsp_state = {"stop": not enable_rtsp_push, "w": push_w, "h": push_h, "bitrate": push_bitrate, "crf": push_crf}
    rtsp_queue = queue.Queue(maxsize=1)

    def start_rtsp_push(w, h, bitrate, crf):
        nonlocal rtsp_proc, rtsp_start_time
        with rtsp_lock:
            rtsp_start_time = time.time()
            rtsp_state["w"] = w
            rtsp_state["h"] = h
            rtsp_state["bitrate"] = bitrate
            rtsp_state["crf"] = crf
            if rtsp_proc:
                try:
                    rtsp_proc.terminate()
                    rtsp_proc.wait(timeout=1.0)
                except Exception:
                    try:
                        rtsp_proc.kill()
                    except Exception:
                        pass
                try:
                    if rtsp_proc.stdin:
                        rtsp_proc.stdin.close()
                except Exception:
                    pass
            
            ffmpeg_push_cmd = [
                "ffmpeg", "-y",
                "-loglevel", "error",
            ]
            if is_live:
                ffmpeg_push_cmd.extend(["-use_wallclock_as_timestamps", "1"])
            ffmpeg_push_cmd.extend([
                "-f", "rawvideo",
                "-pix_fmt", "bgr24",
                "-s", f"{w}x{h}",
                "-r", f"{int(fps) if fps > 0 else 25}",
                "-i", "-",
            ])
            
            gop_size = "15"
            
            if 'M' in bitrate:
                val = float(bitrate.replace('M',''))
                max_r = f"{val*1.5:.1f}M"
                buf_s = f"{val*2:.1f}M"
            else:
                val = float(bitrate.replace('K',''))
                max_r = f"{int(val*1.5)}K"
                buf_s = f"{int(val*2)}K"

            if encoder_to_use == "h264_nvenc":
                ffmpeg_push_cmd.extend([
                    "-vf", "format=yuv420p",
                    "-c:v", "h264_nvenc",
                    "-preset", "p3",
                    "-tune", "ull",
                    "-b:v", bitrate,
                    "-maxrate", max_r,
                    "-bufsize", buf_s,
                    "-g", gop_size,
                    "-forced-idr", "1"
                ])
            elif encoder_to_use == "h264_v4l2m2m":
                ffmpeg_push_cmd.extend([
                    "-vf", "format=yuv420p",
                    "-c:v", "h264_v4l2m2m",
                    "-b:v", bitrate,
                    "-g", gop_size,
                    "-num_output_buffers", "6",
                ])
            else:
                ffmpeg_push_cmd.extend([
                    "-vf", "format=yuv420p",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-tune", "zerolatency",
                    "-crf", crf,
                    "-b:v", bitrate,
                    "-maxrate", max_r,
                    "-bufsize", buf_s,
                    "-g", gop_size
                ])
                
            ffmpeg_push_cmd.extend([
                "-f", "rtsp",
                "-rtsp_transport", "tcp",
                rtsp_target
            ])
            
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                
            try:
                rtsp_proc = subprocess.Popen(
                    ffmpeg_push_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=None,
                    startupinfo=startupinfo
                )
                print(f"[RTSP Push] Khởi động đẩy RTSP: {w}x{h} @ {bitrate} (CRF={crf})")
            except Exception as e:
                print(f"[RTSP Push] Lỗi khởi động FFmpeg push: {e}")

    def rtsp_writer_worker():
        nonlocal encoder_to_use
        while not rtsp_state["stop"]:
            try:
                frame_ref = rtsp_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if frame_ref is None:
                break

            with rtsp_lock:
                proc = rtsp_proc
                pw, ph = rtsp_state["w"], rtsp_state["h"]
            if not proc or not proc.stdin or rtsp_state["stop"]:
                continue

            if frame_ref.shape[1] != pw or frame_ref.shape[0] != ph:
                push_frame = cv2.resize(frame_ref, (pw, ph))
            else:
                push_frame = frame_ref
            if not push_frame.flags["C_CONTIGUOUS"]:
                push_frame = np.ascontiguousarray(push_frame)
            mv = memoryview(push_frame.ravel())

            try:
                proc.stdin.write(mv)
            except Exception as e:
                if rtsp_state["stop"]:
                    break
                with rtsp_lock:
                    if rtsp_proc is not proc or rtsp_state["stop"]:
                        continue
                    if proc.poll() is not None:
                        run_duration = time.time() - rtsp_start_time
                        if run_duration < 3.0 and encoder_to_use != "libx264":
                            print(f"[RTSP Push - Camera {camera_id}] Bộ mã hóa phần cứng '{encoder_to_use}' lỗi ({run_duration:.2f}s). Tự động chuyển về 'libx264'...")
                            encoder_to_use = "libx264"
                            start_rtsp_push(rtsp_state["w"], rtsp_state["h"], rtsp_state["bitrate"], rtsp_state["crf"])
                        elif run_duration >= 2.0:
                            print(f"[RTSP Push] Tiến trình FFmpeg đã ngắt ({e}). Đang khởi động lại...")
                            start_rtsp_push(rtsp_state["w"], rtsp_state["h"], rtsp_state["bitrate"], rtsp_state["crf"])
                    elif (time.time() - rtsp_start_time) >= 5.0:
                        start_rtsp_push(rtsp_state["w"], rtsp_state["h"], rtsp_state["bitrate"], rtsp_state["crf"])

    rtsp_writer_thread = None

    def ensure_rtsp_push_started():
        nonlocal encoder_to_use, rtsp_writer_thread
        if encoder_to_use == "disabled":
            encoder_to_use = _get_preferred_ffmpeg_encoder()
        while not rtsp_queue.empty():
            try:
                rtsp_queue.get_nowait()
            except queue.Empty:
                break
        rtsp_state["stop"] = False
        start_rtsp_push(push_w, push_h, push_bitrate, push_crf)
        if rtsp_writer_thread is None or not rtsp_writer_thread.is_alive():
            rtsp_writer_thread = threading.Thread(target=rtsp_writer_worker, args=(), daemon=True)
            rtsp_writer_thread.start()

    def stop_rtsp_push():
        nonlocal rtsp_proc, rtsp_writer_thread
        rtsp_state["stop"] = True
        with rtsp_lock:
            proc_to_stop = rtsp_proc
            rtsp_proc = None
        if proc_to_stop:
            try:
                proc_to_stop.terminate()
                proc_to_stop.wait(timeout=1.0)
            except Exception:
                try:
                    proc_to_stop.kill()
                except Exception:
                    pass
        if rtsp_writer_thread is not None:
            try:
                rtsp_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                rtsp_queue.put_nowait(None)
            except queue.Full:
                pass
            try:
                rtsp_writer_thread.join(timeout=2.0)
            except Exception:
                pass
            rtsp_writer_thread = None
        if proc_to_stop and proc_to_stop.stdin:
            try:
                proc_to_stop.stdin.close()
            except Exception:
                pass

    if enable_rtsp_push:
        ensure_rtsp_push_started()

    # FIX #4: Tính drawing params 1 lần trước vòng lặp
    f_scale, f_thick, f_offset = _get_drawing_params(draw_w)
    last_progress_time = 0.0
    last_sent_jpeg_seq = 0
    cached_draw_boxes: List[Tuple[int, int, int, int, Tuple[int, int, int], str]] = []
    cached_plate_overlays: List[Tuple[int, int, int, int, Tuple[int, int, int], str]] = []
    cached_status_args: Optional[Tuple[float, str, Tuple[int, int, int]]] = None
    last_infer_frame_index = 0

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
                    resp = progress_callback({
                        "phase": "running_detection",
                        "processed_frames": frame_index,
                        "source_total_frames": total_frames,
                        "progress_percent": None,
                        "elapsed_seconds": round(time.time() - started_at, 1),
                        "latest_status": "Đang tạm dừng...",
                        "preview_jpeg": _encode_preview_frame(p_frame, preview_w, preview_h),
                    })
                    if resp and resp.get("abort"):
                        print(f"[AI - Camera {camera_id}] Nhận tín hiệu dừng khi đang tạm dừng. Đang dừng luồng xử lý...")
                        break
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
                    # Doi voi RTSP / Live camera: Thu doc lai lien tuc khi mang bi giat/lag thay vi thoat ngay
                    retry_success = False
                    for _ in range(15):  # Cho toi da ~15s cho luong RTSP reconnect tu dong
                        time.sleep(1.0)
                        if pause_event and pause_event.is_set():
                            break
                        success_retry, frame_retry = capture.read()
                        if success_retry and frame_retry is not None:
                            frame = frame_retry
                            retry_success = True
                            print(f'[AI - Camera {camera_id}] Luong RTSP da tu dong phuc hoi ket noi thanh cong.')
                            break
                    if not retry_success:
                        print(f'[AI - Camera {camera_id}] Mat ket noi luong RTSP qua 15 giay. Ket thuc phien de bo tu dong phuc hoi khoi dong lai.')
                        break

            # Skip frames corrupted by RTSP packet loss
            if not _is_frame_valid(frame):
                consecutive_invalid_frames += 1
                if consecutive_invalid_frames > MAX_INVALID_FRAMES:
                    print(f'[AI - Cam {camera_id}] Too many consecutive corrupt frames ({consecutive_invalid_frames}). Breaking to trigger auto-heal restart.')
                    break
                continue
            consecutive_invalid_frames = 0

            frame_index += 1
            current_time = time.time()

            # Chỉ lưu ring buffer 1080p khi bật phát hiện đỗ xe VÀ có lưu DB
            # Tránh copy 6.22MB/frame (~933MB RAM buffer) khi chỉ xem Preview hoặc tắt đỗ xe
            if enable_illegal_parking and save_to_db:
                clean_frame = frame.copy()
                parking_manager.update_buffer(clean_frame)
            else:
                clean_frame = None

            # Tracking & Inference theo bước nhảy (Stride)
            if enable_ai and model is not None:
                did_infer = (process_stride <= 1) or (frame_index % process_stride == 0) or (last_results is None)
                if did_infer:
                    results = model.track(frame, persist=True, tracker=tracker_config_path, verbose=False, imgsz=640)
                    last_results = results
                else:
                    results = last_results
            else:
                did_infer = False
                results = []
                cached_draw_boxes.clear()
                cached_plate_overlays.clear()
                cached_status_args = None

            if enable_ai and did_infer:
                frame_step = max(1, frame_index - last_infer_frame_index) if last_infer_frame_index > 0 else 1
                last_infer_frame_index = frame_index

                if enable_illegal_parking and parking_manager is not None:
                    parking_manager.cleanup_stale_tracks(current_time)

                if frame_index % 10 == 0 or frame_step >= 10:
                    stationary_stabilizer.cleanup_old_tracks(frame_index)

                if enable_congestion and traffic_monitor is not None:
                    traffic_monitor.reset_counters()

                cached_draw_boxes.clear()
                cached_plate_overlays.clear()
                current_plate_ids = set()
                current_track_ids = set()
                frame_snapshot = None  # Lazy copy chỉ tạo khi thực sự lưu ảnh xe

                # Duyệt từng result một lần trích xuất Tensor -> NumPy (tránh Python slicing lặp)
                for r_idx, result in enumerate(results):
                    boxes_obj = result.boxes
                    if boxes_obj is None or len(boxes_obj) == 0:
                        continue

                    xyxy_arr = boxes_obj.xyxy.cpu().numpy().astype(np.int32)
                    conf_arr = boxes_obj.conf.cpu().numpy()
                    cls_arr = boxes_obj.cls.cpu().numpy().astype(np.int32)
                    id_arr = boxes_obj.id.cpu().numpy().astype(np.int32) if boxes_obj.id is not None else None
                    n_boxes = len(xyxy_arr)

                    parking_indices = []
                    plate_candidates = []  # (px1, py1, px2, py2, p_tid)
                    labels_arr = [""] * n_boxes
                    needs_clean_snap = False

                    # Pass chuẩn bị duy nhất trên mảng NumPy
                    for b_idx in range(n_boxes):
                        c_id = int(cls_arr[b_idx])
                        lbl = canon_names_map.get(c_id)
                        if lbl is None:
                            names_src = getattr(model, "custom_names", None)
                            if not names_src:
                                names_src = getattr(model, "names", None) or {}
                            if isinstance(names_src, dict):
                                lbl_name = names_src.get(c_id, c_id)
                            elif isinstance(names_src, (list, tuple)) and 0 <= c_id < len(names_src):
                                lbl_name = names_src[c_id]
                            else:
                                lbl_name = c_id
                            lbl = _canonical_label(lbl_name)
                            canon_names_map[c_id] = lbl
                            disp_names_map[c_id] = _display_label(lbl)
                        labels_arr[b_idx] = lbl

                        tid_raw = int(id_arr[b_idx]) if id_arr is not None else -1
                        if tid_raw != -1:
                            current_track_ids.add(tid_raw)
                            current_track_ids.add(stationary_stabilizer.raw_to_canonical.get(tid_raw, tid_raw))

                        if lbl in PARKING_LABELS:
                            parking_indices.append(b_idx)
                        elif lbl in LICENSE_PLATE_LABELS and enable_license_plate and float(conf_arr[b_idx]) >= confidence_threshold:
                            plate_candidates.append((
                                int(xyxy_arr[b_idx, 0]),
                                int(xyxy_arr[b_idx, 1]),
                                int(xyxy_arr[b_idx, 2]),
                                int(xyxy_arr[b_idx, 3]),
                                tid_raw,
                            ))

                        if not needs_clean_snap and clean_frame is None and float(conf_arr[b_idx]) >= confidence_threshold:
                            if (
                                (enable_license_plate and lbl in LICENSE_PLATE_LABELS)
                                or (enable_weapon_detection and lbl in WEAPON_LABELS)
                                or (enable_illegal_parking and lbl in PARKING_LABELS)
                                or (save_to_db and lbl in VEHICLE_LOG_LABELS and lbl not in VEHICLE_NO_IMAGE_LABELS)
                            ):
                                needs_clean_snap = True

                    # Nếu frame có đối tượng cần cắt ảnh (OCR/ALPR/Weapon) và chưa copy clean_frame -> copy 1 lần trước khi vẽ
                    if needs_clean_snap and clean_frame is None:
                        clean_frame = frame.copy()

                    # Lọc các box xe trùng lặp trong cùng 1 frame bằng mảng NumPy
                    supp_set = stationary_stabilizer.filter_duplicate_indices_np(xyxy_arr, conf_arr, parking_indices)

                    # Thu thập valid_vehicles và khóa Canonical ID một lần duy nhất
                    valid_vehicles = []
                    canonical_by_idx = {}
                    for b_idx in parking_indices:
                        if b_idx in supp_set:
                            continue
                        vx1, vy1, vx2, vy2 = int(xyxy_arr[b_idx, 0]), int(xyxy_arr[b_idx, 1]), int(xyxy_arr[b_idx, 2]), int(xyxy_arr[b_idx, 3])
                        v_track_id = int(id_arr[b_idx]) if id_arr is not None else -1
                        vcx, vcy = (vx1 + vx2) // 2, (vy1 + vy2) // 2
                        stable_v_id = stationary_stabilizer.get_canonical_id(
                            v_track_id, (vx1, vy1, vx2, vy2), (vcx, vcy), frame_index
                        ) if v_track_id != -1 else v_track_id
                        canonical_by_idx[b_idx] = stable_v_id
                        valid_vehicles.append((vx1, vy1, vx2, vy2, stable_v_id))
                        if stable_v_id != -1:
                            current_track_ids.add(stable_v_id)

                    # Vòng lặp chính xử lý detection (dùng dữ liệu NumPy đã trích xuất)
                    for b_idx in range(n_boxes):
                        if b_idx in supp_set:
                            continue
                        label = labels_arr[b_idx]
                        if label not in DETECTABLE_LABELS:
                            continue
                        if label in LICENSE_PLATE_LABELS and not enable_license_plate:
                            continue
                        if label in WEAPON_LABELS and not enable_weapon_detection:
                            continue
                        if enabled_detect_labels is not None and label not in enabled_detect_labels:
                            continue

                        confidence = float(conf_arr[b_idx])
                        if confidence < confidence_threshold:
                            continue

                        track_id = int(id_arr[b_idx]) if id_arr is not None else -1
                        x1, y1, x2, y2 = int(xyxy_arr[b_idx, 0]), int(xyxy_arr[b_idx, 1]), int(xyxy_arr[b_idx, 2]), int(xyxy_arr[b_idx, 3])
                        center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2

                        if cv2.pointPolygonTest(roi_polygon, (center_x, center_y), False) < 0:
                            continue

                        box_area = (x2 - x1) * (y2 - y1)
                        if label in SMALL_OBJECT_LABELS and roi_contour_area > 0 and box_area > roi_contour_area * 0.3:
                            continue

                        box_color = BOX_COLORS.get(label, (128, 128, 128))
                        display_label = disp_names_map.get(int(cls_arr[b_idx]), _display_label(label)) if show_label else ""
                        stable_track_id = canonical_by_idx.get(b_idx, track_id)

                        # 1. OCR Biển số
                        if label in LICENSE_PLATE_LABELS:
                            if enable_license_plate and track_id != -1:
                                src_clean = clean_frame if clean_frame is not None else frame
                                processed_id = ocr_manager.process_plate(
                                    frame, src_clean, track_id, x1, y1, x2, y2, center_x, center_y,
                                    valid_vehicles, current_time, frame_index,
                                    drawing_params=(f_scale, f_thick, f_offset)
                                )
                                if processed_id:
                                    current_plate_ids.add(processed_id)
                                if ocr_manager.last_drawn_overlay is not None:
                                    cached_plate_overlays.append(ocr_manager.last_drawn_overlay)
                            continue

                        # 2. Đếm phương tiện và người
                        if label == "person":
                            if traffic_monitor is not None:
                                traffic_monitor.log_person(bbox=(x1, y1, x2, y2))
                        elif label in VEHICLE_LABELS and traffic_monitor is not None:
                            traffic_monitor.log_vehicle(stable_track_id, center_x, center_y, current_time, (x1, y1, x2, y2))

                        # 3. Quản lý Đỗ Xe Trái Phép
                        if label in PARKING_LABELS and enable_illegal_parking:
                            license_plate = None
                            if enable_license_plate and plate_candidates:
                                for px1, py1, px2, py2, p_tid in plate_candidates:
                                    pcx, pcy = (px1 + px2) // 2, (py1 + py2) // 2
                                    if x1 <= pcx <= x2 and y1 <= pcy <= y2:
                                        if p_tid in ocr_manager.plate_confirmed:
                                            license_plate = ocr_manager.plate_confirmed[p_tid]
                                        break
                            if not license_plate and enable_license_plate and ocr_manager:
                                license_plate = ocr_manager.vehicle_plates.get(stable_track_id) or ocr_manager.vehicle_plates.get(track_id)

                            if license_plate:
                                stable_track_id = stationary_stabilizer.get_canonical_id(
                                    track_id, (x1, y1, x2, y2), (center_x, center_y), frame_index, plate=license_plate
                                )

                            src_clean = clean_frame if clean_frame is not None else frame
                            display_label_p, box_color_p = parking_manager.process_vehicle(
                                frame, src_clean, stable_track_id, label, center_x, center_y, frame_index,
                                bbox=(x1, y1, x2, y2), license_plate=license_plate,
                                drawing_params=(f_scale, f_thick, f_offset)
                            )

                            if display_label_p:
                                display_label = display_label_p if show_label else ""
                                if license_plate:
                                    parking_manager.update_plate(stable_track_id, license_plate)
                                if "VIOLATION" in display_label_p and stable_track_id not in logged_violation_track_ids:
                                    logged_violation_track_ids.add(stable_track_id)

                            if box_color_p is not None:
                                box_color = box_color_p

                        # 3.5. Quản lý Phát hiện Vũ khí (Weapon Detection)
                        if label in WEAPON_LABELS and enable_weapon_detection and weapon_manager is not None:
                            src_clean = clean_frame if clean_frame is not None else frame
                            display_label_w, box_color_w = weapon_manager.process_weapon(
                                frame, src_clean, track_id, label, confidence,
                                bbox=(x1, y1, x2, y2),
                                camera_id=camera_id, camera_name=settings.get("name", "Camera"),
                                telegram_enabled=bool(settings.get("telegram_enabled", save_to_db))
                            )
                            if display_label_w:
                                display_label = display_label_w if show_label else ""
                                box_color = box_color_w

                        # 4. Quản lý theo dõi ALPR và Log phương tiện (Đợi xe ổn định mới lưu)
                        alpr_tid = stable_track_id if (label in PARKING_LABELS and track_id != -1) else track_id
                        if alpr_tid != -1 and label in VEHICLE_LOG_LABELS:
                            if alpr_tid not in pending_alpr_tracks:
                                pending_alpr_tracks[alpr_tid] = {
                                    "last_seen": frame_index,
                                    "best_image": None,
                                    "seen_count": frame_step,
                                    "is_passed_logged": False,
                                    "missing_frames": 0,
                                    "label": label,
                                    "best_w": (x2 - x1) if label not in VEHICLE_NO_IMAGE_LABELS else 0
                                }
                                if label not in VEHICLE_NO_IMAGE_LABELS and save_to_db:
                                    if frame_snapshot is None:
                                        frame_snapshot = (clean_frame if clean_frame is not None else frame).copy()
                                    pending_alpr_tracks[alpr_tid]["best_image"] = (frame_snapshot, (x1, y1, x2, y2))
                            else:
                                pending_alpr_tracks[alpr_tid]["last_seen"] = frame_index
                                pending_alpr_tracks[alpr_tid]["seen_count"] += frame_step
                                pending_alpr_tracks[alpr_tid]["missing_frames"] = 0

                                if label not in VEHICLE_NO_IMAGE_LABELS and save_to_db:
                                    if (x2 - x1) > pending_alpr_tracks[alpr_tid]["best_w"]:
                                        if frame_snapshot is None:
                                            frame_snapshot = (clean_frame if clean_frame is not None else frame).copy()
                                        pending_alpr_tracks[alpr_tid]["best_image"] = (frame_snapshot, (x1, y1, x2, y2))
                                        pending_alpr_tracks[alpr_tid]["best_w"] = (x2 - x1)

                                # XÁC MINH XE MỚI: Chỉ lưu vào DB sau khi đã thấy xe ổn định (> 30 frames ~ 1s)
                                if not pending_alpr_tracks[alpr_tid]["is_passed_logged"] and pending_alpr_tracks[alpr_tid]["seen_count"] > 30:
                                    if save_to_db:
                                        io_worker.enqueue_db_write(log_passed_vehicle, args=(camera_id, f"ID_{alpr_tid}", label))
                                        io_worker.enqueue_db_write(log_vehicle_count, args=(camera_id, label, 1))

                                    logged_vehicle_ids.add(alpr_tid)
                                    unique_passed_count += 1
                                    pending_alpr_tracks[alpr_tid]["is_passed_logged"] = True

                        # 5. Vẽ nhãn lên frame nếu được bật (và cache để dùng lại cho frame skip)
                        if label == "person" and enable_congestion:
                            show_box = True
                        elif enabled_draw_labels is not None and len(enabled_draw_labels) > 0:
                            show_box = label in enabled_draw_labels
                        else:
                            show_box = show_box_map.get(label, True)

                        if show_box:
                            _draw_detection_box(frame, x1, y1, x2, y2, box_color, display_label, f_scale, f_thick, show_label)
                            cached_draw_boxes.append((x1, y1, x2, y2, box_color, display_label))

                # --- LOGIC DỌN DẸP VÀ CHỐT LOG (VỚI THỜI GIAN ĐỢI RE-ID) ---
                for tid in list(pending_alpr_tracks.keys()):
                    if tid not in current_track_ids:
                        pending_alpr_tracks[tid]["missing_frames"] += frame_step
                        if pending_alpr_tracks[tid]["missing_frames"] > 45:
                            if pending_alpr_tracks[tid]["is_passed_logged"]:
                                if tid not in alpr_logger.logged_v_tracks and save_to_db and pending_alpr_tracks[tid]["best_image"] is not None:
                                    best_f, best_box = pending_alpr_tracks[tid]["best_image"]
                                    alpr_logger.log_vehicle_without_plate(frame_index, best_f, best_box)
                            del pending_alpr_tracks[tid]

                # Cập nhật Traffic Monitor trên frame có inference mới
                if enable_congestion and traffic_monitor is not None:
                    avg_spd, raw_st_txt, raw_st_clr, raw_lvl = traffic_monitor.calculate_speed_and_status(current_time, frame.shape)
                    # Truyền trực tiếp frame (không .copy() lãng phí 6.22MB mỗi frame)
                    traffic_alert_manager.update_traffic_state(raw_lvl, frame)
                    confirmed_lvl = traffic_alert_manager.confirmed_level
                    conf_st_txt, conf_st_clr = traffic_monitor.get_status_for_level(confirmed_lvl, avg_spd)
                    cached_status_args = (avg_spd, conf_st_txt, conf_st_clr)

                    is_admin = bool(settings.get("is_admin", False))
                    traffic_monitor.draw_status(frame, avg_spd, conf_st_txt, conf_st_clr, f_scale, f_thick, is_admin=is_admin)
                    latest_status = conf_st_txt

                    # --- ĐỒNG BỘ DATABASE (Sync based on Confirmed Level) ---
                    if confirmed_lvl != last_db_traffic_level:
                        if last_congestion_record_id and save_to_db:
                            io_worker.enqueue_db_write(update_congestion_end_time, args=(last_congestion_record_id,))
                            last_congestion_record_id = None

                        if confirmed_lvl > 0 and save_to_db:
                            now = datetime.now()
                            lvl_name = TRAFFIC_LEVEL_NAMES.get(confirmed_lvl, "UnTac")
                            time_folder = now.strftime(f"{lvl_name}_%H%M")

                            rel_dir = os.path.join(
                                "logs", "traffic",
                                now.strftime('%Y'),
                                now.strftime('%m'),
                                now.strftime('%d'),
                                time_folder
                            )
                            abs_dir = PROJECT_ROOT / rel_dir
                            os.makedirs(abs_dir, exist_ok=True)

                            img_name = f"{time_folder}.jpg"
                            rel_path = os.path.join(rel_dir, img_name).replace("\\", "/")
                            abs_path = str(abs_dir / img_name)

                            # enqueue_save_image tự copy bên trong -> không gọi frame.copy() trùng
                            io_worker.enqueue_save_image(abs_path, frame)
                            last_congestion_record_id = log_congestion(camera_id, confirmed_lvl, duong_dan_anh=rel_path)

                        last_db_traffic_level = confirmed_lvl

            elif enable_ai:
                # Frame skip: chỉ vẽ lại nhanh các box/nhãn/status đã cache (không tốn CPU cho AI/Mask/Stabilizer)
                for bx1, by1, bx2, by2, bcol, blbl in cached_draw_boxes:
                    _draw_detection_box(frame, bx1, by1, bx2, by2, bcol, blbl, f_scale, f_thick, show_label)
                for px1, py1, px2, py2, pcol, ptxt in cached_plate_overlays:
                    _draw_plate_overlay(frame, px1, py1, px2, py2, pcol, ptxt, f_scale, f_thick, f_offset)
                if enable_congestion and traffic_monitor is not None and cached_status_args is not None:
                    is_admin = bool(settings.get("is_admin", False))
                    traffic_monitor.draw_status(frame, *cached_status_args, f_scale, f_thick, is_admin=is_admin)

            # Gửi heartbeat định kỳ (mỗi 10 giây)
            if current_time - last_heartbeat_time >= 10.0:
                last_heartbeat_time = current_time
                if last_congestion_record_id and save_to_db:
                    io_worker.enqueue_db_write(update_congestion_heartbeat, args=(last_congestion_record_id,))

                if enable_illegal_parking and parking_manager and parking_manager.violation_records:
                    for tid, v_id in list(parking_manager.violation_records.items()):
                        io_worker.enqueue_db_write(update_violation_heartbeat, args=(v_id,))

            # --- VẼ CÁC VÙNG QUAN SÁT (ROI OVERLAY) ---
            if show_roi_surveillance and roi_polygon is not None and len(roi_polygon) > 0:
                cv2.polylines(frame, [roi_polygon], True, (255, 120, 0), f_thick)
                cv2.putText(frame, "VUNG GIAM SAT (ROI)", (int(roi_polygon[0][0]), int(roi_polygon[0][1] - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, f_scale * 0.7, (255, 255, 255), f_thick)

            if show_roi_parking and parking_manager is not None:
                parking_manager.draw_polygon_overlay(frame, f_thick)

            # Tính và vẽ FPS
            fps_frame_count += 1
            fps_now = time.time()
            if fps_now - fps_prev_time >= 1.0:
                current_fps = fps_frame_count / (fps_now - fps_prev_time)
                fps_prev_time = fps_now
                fps_frame_count = 0
                accumulated_process_time = 0.0
            if preview_state["viewer_count"] > 0 and (fps_now - diag_prev_time >= 10.0):
                diag_elapsed = fps_now - diag_prev_time
                with preview_lock:
                    enc_cnt = preview_state["enc_count"]
                    preview_state["enc_count"] = 0
                backend_name = "GStreamer" if getattr(capture, "_using_gstreamer", False) else "FFmpeg"
                print(
                    f"[Stream Diag - Cam {camera_id}] backend={backend_name} | "
                    f"loop_fps={current_fps:.1f} | enc_fps={enc_cnt / diag_elapsed:.1f} | "
                    f"viewers={preview_state['viewer_count']} | rtsp_push={enable_rtsp_push}"
                )
                diag_prev_time = fps_now
            if show_fps:
                cv2.putText(frame, f"FPS: {int(current_fps)}", (30, draw_h - 40),
                            cv2.FONT_HERSHEY_SIMPLEX, f_scale, (0, 255, 255), f_thick)

            # Đẩy frame sang RTSP writer thread bất đồng bộ (không block vòng lặp AI)
            if enable_rtsp_push and not rtsp_state["stop"]:
                try:
                    rtsp_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    rtsp_queue.put_nowait(frame)
                except queue.Full:
                    pass

            response = None
            # 3. Nạp frame mới nhất vào bộ nén JPEG nền (nén xong sẽ đẩy tức thì) & đồng bộ trạng thái
            if progress_callback is not None:
                now_t = time.time()

                # Tách việc nạp frame khỏi throttle IPC để không bị bỏ lỡ frame skip / jitter RTSP.
                if preview_state["viewer_count"] > 0 or preview_state["force_preview"]:
                    try:
                        preview_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        preview_queue.put_nowait((frame, frame_index, latest_status))
                    except queue.Full:
                        pass

                status_interval = 0.25 if preview_state["viewer_count"] > 0 else 0.5
                if (now_t - preview_state["last_emit_time"] >= status_interval) or (frame_index == 1):
                    preview_state["last_emit_time"] = now_t
                    _emit_progress({
                        "phase": "running_detection",
                        "latest_status": latest_status,
                        "timestamp": now_t,
                        "processed_frames": frame_index,
                    })

                with pending_actions_lock:
                    response = dict(pending_actions) if pending_actions else None
                    pending_actions.clear()
                if response and response.get("abort"):
                    print(f"[AI - Camera {camera_id}] Nhận tín hiệu dừng từ hệ thống. Đang dừng luồng xử lý...")
                    break

                # Xử lý lệnh từ Manager (ví dụ đổi chất lượng)
                if response and "new_quality" in response:
                    q = response["new_quality"]
                    profile = _stream_quality_profiles.get(q)
                    if profile:
                        pw, ph, pq, push_w, push_h, push_bitrate, push_crf = profile
                        preview_state["pw"], preview_state["ph"] = pw, ph
                        preview_state["q"] = pq
                        push_w = push_w + (push_w % 2)
                        push_h = push_h + (push_h % 2)
                        if enable_rtsp_push:
                            start_rtsp_push(push_w, push_h, push_bitrate, push_crf)
                        print(f"[AI] Đã đổi chất lượng sang: {q} ({preview_state['pw']}x{preview_state['ph']}, quality={preview_state['q']})")
                
                # Xử lý lệnh từ Manager (đổi cài đặt cấu hình AI từ Front-end)
                if response and "new_settings" in response:
                    new_s = response["new_settings"]
                    
                    # Cập nhật nóng các thông số kỹ thuật AI (Chỉ ghi nhận và in log khi có sự thay đổi thực sự)
                    if "confidence" in new_s:
                        val = float(new_s["confidence"])
                        if val != confidence_threshold:
                            confidence_threshold = val
                            print(f"[Dynamic Settings] Confidence threshold updated to: {confidence_threshold}")
                    
                    if "frame_skip" in new_s:
                        val = max(1, int(new_s["frame_skip"]))
                        if val != process_stride:
                            process_stride = val
                            print(f"[Dynamic Settings] Process stride (frame_skip) updated to: {process_stride}")
                        
                    if "congestion_threshold" in new_s:
                        val = float(new_s["congestion_threshold"])
                        if val != congestion_threshold:
                            congestion_threshold = val
                            if traffic_monitor is not None:
                                traffic_monitor.congestion_threshold = congestion_threshold
                            print(f"[Dynamic Settings] Congestion threshold updated to: {congestion_threshold}%")
                        
                    if "parking_violation_time" in new_s:
                        val = float(new_s["parking_violation_time"])
                        if val != stop_seconds:
                            stop_seconds = val
                            if parking_manager is not None:
                                parking_manager.stop_seconds = stop_seconds
                            print(f"[Dynamic Settings] Parking violation time (stop_seconds) updated to: {stop_seconds}s")
                    
                    if "no_parking_points" in new_s or "no_park_meta" in new_s:
                        new_np = new_s.get("no_parking_points", settings.get("no_parking_points"))
                        new_meta = new_s.get("no_park_meta", settings.get("no_park_meta"))
                        updated_zones = _normalize_parking_zones(new_np, draw_w, draw_h, new_meta)
                        if parking_manager is not None:
                            parking_manager.set_parking_zones(updated_zones)
                        print(f"[Dynamic Settings] Parking zones updated: {len(updated_zones)} zone(s)")
                    
                    if "is_admin" in new_s:
                        val = bool(new_s["is_admin"])
                        if settings.get("is_admin") != val:
                            settings["is_admin"] = val
                            print(f"[Dynamic Settings] Vai trò người xem (is_admin) updated to: {settings['is_admin']}")

                    # Bật tắt xử lý AI
                    if "enable_ai" in new_s:
                        db_enable_ai = new_s["enable_ai"]
                        if db_enable_ai != enable_ai:
                            enable_ai = db_enable_ai
                            print(f"[Dynamic Settings] Xử lý AI updated to: {enable_ai}")
                            if enable_ai and model is None:
                                print("[Dynamic Settings] Loading YOLO model...")
                                model = _load_model(model_path)
                                canon_names_map, disp_names_map = _build_label_lookup(model)
                    
                    # Bật tắt phát hiện tắc nghẽn
                    if "enable_congestion" in new_s:
                        db_enable_congestion = new_s["enable_congestion"] if (new_s.get("enable_ai") if "enable_ai" in new_s else enable_ai) else False
                        if db_enable_congestion != enable_congestion:
                            enable_congestion = db_enable_congestion
                            print(f"[Dynamic Settings] Phát hiện tắc nghẽn updated to: {enable_congestion}")
                            if enable_congestion and traffic_monitor is None:
                                traffic_monitor = TrafficMonitor(roi_polygon=roi_polygon, congestion_threshold=congestion_threshold)
                            elif not enable_congestion:
                                if last_congestion_record_id and save_to_db:
                                    io_worker.enqueue_db_write(update_congestion_end_time, args=(last_congestion_record_id,))
                                    last_congestion_record_id = None
                                last_db_traffic_level = 0
                    
                    # Bật tắt phát hiện đỗ xe trái phép
                    if "enable_illegal_parking" in new_s:
                        db_enable_illegal_parking = new_s["enable_illegal_parking"] if (new_s.get("enable_ai") if "enable_ai" in new_s else enable_ai) else False
                        if db_enable_illegal_parking != enable_illegal_parking:
                            enable_illegal_parking = db_enable_illegal_parking
                            print(f"[Dynamic Settings] Phát hiện đỗ trái phép updated to: {enable_illegal_parking}")
                            if enable_illegal_parking:
                                parking_manager.setup_detection(fps)
                    
                    # Bật tắt nhận diện biển số
                    if "enable_license_plate" in new_s:
                        db_enable_license_plate = new_s["enable_license_plate"] if (new_s.get("enable_ai") if "enable_ai" in new_s else enable_ai) else False
                        if db_enable_license_plate != enable_license_plate:
                            enable_license_plate = db_enable_license_plate
                            print(f"[Dynamic Settings] Nhận diện biển số updated to: {enable_license_plate}")
                            if enable_license_plate and ocr_manager is None:
                                print("[Dynamic Settings] Loading PaddleOCR model...")
                                ocr_reader = create_paddle_ocr()
                                ocr_manager = OCRManager(ocr_reader, alpr_logger=alpr_logger)
                                ocr_manager.save_to_db = save_to_db
                                ocr_manager.start_worker()
                    
                    # Cập nhật cài đặt hiển thị (Overlay settings) từ Front-end
                    if "show_roi_surveillance" in new_s:
                        show_roi_surveillance = bool(new_s["show_roi_surveillance"])
                    if "show_roi_parking" in new_s:
                        show_roi_parking = bool(new_s["show_roi_parking"])
                    if "show_fps" in new_s:
                        show_fps = bool(new_s["show_fps"])
                    for key_name, lbl_key in (
                        ("show_box_person", "person"),
                        ("show_box_bicycle", "bicycle"),
                        ("show_box_car", "car"),
                        ("show_box_motorcycle", "motorcycle"),
                        ("show_box_plate", "license_plate"),
                        ("show_box_bus", "bus"),
                        ("show_box_truck", "truck"),
                        ("show_box_knife", "knife"),
                        ("show_box_pistol", "pistol"),
                        ("show_box_sword", "sword"),
                        ("show_box_helmet", "helmet"),
                        ("show_box_mask", "mask"),
                        ("show_box_backpack", "backpack"),
                    ):
                        if key_name in new_s:
                            show_box_map[lbl_key] = bool(new_s[key_name])
                    if "show_label" in new_s:
                        show_label = bool(new_s["show_label"])

                    if "enable_rtsp_push" in new_s:
                        new_rtsp_flag = bool(new_s["enable_rtsp_push"])
                        if new_rtsp_flag != enable_rtsp_push:
                            enable_rtsp_push = new_rtsp_flag
                            if enable_rtsp_push:
                                ensure_rtsp_push_started()
                                print(f"[Dynamic Settings - Camera {camera_id}] Đã bật đẩy RTSP nền (WebRTC).")
                            else:
                                stop_rtsp_push()
                                print(f"[Dynamic Settings - Camera {camera_id}] Đã tắt đẩy RTSP nền (MJPEG).")

            # 4. THROTTLE & PROFILING
            now_pace = time.time()
            elapsed = now_pace - frame_start_time
            accumulated_process_time += elapsed

            # Chỉ áp dụng hãm tốc độ (Throttle) cho file video cục bộ theo deadline tích lũy
            # để tránh stride > 1 làm tụt FPS thực khi khung hình có suy luận nặng hơn ideal_frame_time.
            if not is_live and ideal_frame_time > 0:
                next_dl = preview_state["mp4_next_deadline"]
                next_dl = (frame_start_time + ideal_frame_time) if next_dl is None else (next_dl + ideal_frame_time)
                sleep_for = next_dl - now_pace
                if sleep_for > 0:
                    time.sleep(sleep_for)
                elif sleep_for < -ideal_frame_time * 2.0:
                    next_dl = now_pace
                preview_state["mp4_next_deadline"] = next_dl

    finally:
        # Đặt cờ dừng RTSP writer và chấm dứt FFmpeg push trước để giải phóng ngay nếu đang block stdin.write
        stop_rtsp_push()

        preview_state["stop"] = True
        try:
            preview_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            preview_queue.put_nowait(None)
        except queue.Full:
            pass
        try:
            preview_thread.join(timeout=1.0)
        except Exception:
            pass
        capture.release()
        if enable_license_plate and ocr_manager is not None:
            ocr_manager.stop_worker()
        if last_congestion_record_id and save_to_db:
            update_congestion_end_time(last_congestion_record_id)
        # Đã cập nhật thống kê xe theo thời gian thực (realtime) ở vòng lặp chính qua io_worker, không ghi đúp khi shutdown
        if parking_manager and parking_manager.violation_records:
            try:
                print(f"[DetectionBridge] Đang dọn dẹp {len(parking_manager.violation_records)} vi phạm chưa kết thúc...")
                for tid, v_id in list(parking_manager.violation_records.items()):
                    print(f"[DetectionBridge] Cập nhật thời gian kết thúc cho vi phạm {v_id}")
                    update_violation_end_time(v_id)
            except Exception as e:
                print(f"[DetectionBridge] Lỗi khi kết thúc vi phạm đỗ xe: {e}")
            finally:
                parking_manager.violation_records.clear()
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